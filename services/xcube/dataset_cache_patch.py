"""Monkeypatches xcube's DatasetsContext to use a bounded, LRU-evicting
dataset cache instead of the library's own unbounded plain dict.

Root cause (found 2026-08-16, from the 2026-08-15 tile-concurrency
performance investigation's OOM findings):
xcube.webapi.datasets.context.DatasetsContext keeps
`self._dataset_cache = dict()` -- once a dataset is opened, it (and
every pyramid level realized for it, via LazyMultiLevelDataset's own
`_level_datasets` cache) stays resident for the life of the process, no
eviction. Growth tracks the number of distinct (dataset, zoom-level)
pairs ever requested, not dataset count alone (~23MB per
newly-realized zoom level on a dataset). Over long uptimes this reaches
xcube.service's own MemoryHigh=5G guardrail and the process thrashes
into unresponsiveness (happened twice, including a 2026-08-16
recurrence).

This patch bounds `_dataset_cache` to XCUBE_DATASET_CACHE_MAXSIZE
entries (default 40), evicting the least-recently-used dataset once
that limit is exceeded. A cache miss after eviction just means the next
request for that dataset pays the ~1-5s open/decode cost again --
a tradeoff, but one already made implicitly and unboundedly worse
by the leak this replaces.

## 2026-08-17 fix: deferred, serialized close

The first version of this patch (2026-08-16) called `ml_dataset.close()`
synchronously, inline, on whatever request thread triggered the
capacity-exceeding insert. This caused a regression, 2026-08-16 19:27
through 2026-08-17: `/datasets` (and tile
requests) started throwing `RuntimeError: NetCDF: HDF error` --
not file corruption (the same file opens fine via a plain,
isolated `netCDF4.Dataset()` call moments later).

Root cause: xcube dispatches requests via a thread pool
(`tornado.py`'s `run_in_executor`), and `prewarm_cache.py` fires
genuinely concurrent tile requests (`TILE_FETCH_CONCURRENCY` threads).
Eviction here is purely capacity-based (LRU order), not usage-based --
an entry can still have an in-flight reader even though it's the
least-recently-*fetched* entry, if enough *other* distinct new keys
arrive within a few seconds (~40 evictions inside a
single 6-second window during a prewarm sweep). netCDF4/HDF5 is not
thread-safe against a `close()` racing a concurrent read in the same
process -- closing an evicted dataset out from under another thread's
in-flight read corrupts shared HDF5 library state, which then surfaces
as a generic "HDF error" on the *next* unrelated open, not necessarily
on the file actually being closed. This is the same general hazard
already found and fixed for geosat-mosaic-api.

Fix: evicted datasets are no longer closed inline. They're handed to a
single dedicated background thread, which closes them only after
`XCUBE_DATASET_CACHE_CLOSE_DELAY` seconds (default 5) have elapsed
since eviction -- giving any in-flight read (typically well under a
second for one tile) time to finish first. Processing is strictly
serial and ordered by eviction time, so a burst of N evictions does NOT
mean the last one waits N*delay seconds (each item's wait is computed
against its own queue time, and by the time item 1's delay has elapsed,
items 2..N queued moments later are already at or near their own
deadline). Not a provably race-free fix -- a read slower than the delay
could still collide -- but it converts the failure mode from "routine
under a prewarm burst" to "requires a multi-second single-tile read",
which actual tile reads don't approach.
"""

import collections
import logging
import os
import queue
import threading
import time

LOG = logging.getLogger("xcube.dataset_cache_patch")

_DEFAULT_MAXSIZE = 40
_DEFAULT_CLOSE_DELAY_SECONDS = 5.0

_close_queue: "queue.Queue" = queue.Queue()
_closer_lock = threading.Lock()
_closer_started = False


def _closer_loop(delay_seconds: float) -> None:
    """Background thread body: serially closes evicted datasets after a
    delay.

    Runs forever, pulling `(evicted_id, ml_dataset, queued_at)` tuples off
    `_close_queue` one at a time (strictly serial, in eviction order) and
    closing each dataset only once `delay_seconds` have elapsed since it
    was queued -- see the module docstring's "2026-08-17 fix" section for
    why the delay and the serialization both matter.

    Args:
        delay_seconds: Minimum time to wait, from the moment an item was
            queued, before closing it. Read once from the queued item's
            own timestamp, so a burst of evictions doesn't make later
            items wait an additional `delay_seconds` each.
    """
    while True:
        evicted_id, ml_dataset, queued_at = _close_queue.get()
        remaining = delay_seconds - (time.monotonic() - queued_at)
        if remaining > 0:
            time.sleep(remaining)
        try:
            ml_dataset.close()
        except Exception:
            LOG.warning(
                "failed to close evicted dataset %r", evicted_id, exc_info=True
            )
        else:
            # .warning(), not .info() -- xcube's console log level
            # defaults to WARNING (no -v flag passed), so .info() here
            # would be silently swallowed and make the patch look
            # inert even while it's genuinely evicting/closing.
            LOG.warning("evicted dataset %r closed (deferred)", evicted_id)
        _close_queue.task_done()


def _ensure_closer_started(delay_seconds: float) -> None:
    """Starts the single background closer thread the first time it's
    needed, idempotently.

    Safe to call from multiple `_BoundedDatasetCache` instances (only one
    thread is ever started, guarded by `_closer_lock`); subsequent calls
    are no-ops.

    Args:
        delay_seconds: Close delay to pass to `_closer_loop` when starting
            the thread.
    """
    global _closer_started
    with _closer_lock:
        if not _closer_started:
            threading.Thread(
                target=_closer_loop,
                args=(delay_seconds,),
                daemon=True,
                name="xcube-dataset-cache-closer",
            ).start()
            _closer_started = True


class _BoundedDatasetCache(collections.OrderedDict):
    """Drop-in replacement for xcube's `DatasetsContext._dataset_cache`
    plain dict: an LRU-evicting `OrderedDict` bounded to `maxsize`
    entries, with eviction closing deferred to a background thread.

    Access order is tracked via `move_to_end` on both read (`__getitem__`)
    and write (`__setitem__`), so the entry evicted once the cache exceeds
    `maxsize` is always the least-recently-used one. See the module
    docstring for why closing evicted datasets is deferred rather than
    done inline.
    """

    def __init__(self, maxsize: int, close_delay_seconds: float):
        """Initializes the bounded cache and starts the shared background
        closer thread if it isn't already running.

        Args:
            maxsize: Maximum number of dataset entries to keep resident
                before the least-recently-used one is evicted.
            close_delay_seconds: Seconds to wait after eviction before an
                evicted dataset is actually closed (passed through to
                `_ensure_closer_started`).
        """
        super().__init__()
        self._maxsize = maxsize
        self._close_delay_seconds = close_delay_seconds
        _ensure_closer_started(close_delay_seconds)

    def __setitem__(self, key, value):
        """Inserts or updates an entry, then evicts least-recently-used
        entries (queuing each for deferred close) until the cache is back
        at or under `maxsize`.

        Args:
            key: Dataset id.
            value: The `(ml_dataset, dataset_config)` tuple xcube stores
                for this dataset id.
        """
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        while len(self) > self._maxsize:
            evicted_id, evicted_value = self.popitem(last=False)
            ml_dataset, _dataset_config = evicted_value
            LOG.warning(
                "evicting dataset %r from cache (maxsize=%d), close deferred %.1fs",
                evicted_id,
                self._maxsize,
                self._close_delay_seconds,
            )
            _close_queue.put((evicted_id, ml_dataset, time.monotonic()))

    def __getitem__(self, key):
        """Looks up an entry and marks it most-recently-used.

        Args:
            key: Dataset id.

        Returns:
            The `(ml_dataset, dataset_config)` tuple stored for this key.

        Raises:
            KeyError: If `key` is not present in the cache.
        """
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value


def apply() -> None:
    """Monkeypatches `xcube.webapi.datasets.context.DatasetsContext.__init__`
    so every `DatasetsContext` instance gets a `_BoundedDatasetCache`
    instead of the library's plain unbounded dict.

    Reads `XCUBE_DATASET_CACHE_MAXSIZE` (default 40) and
    `XCUBE_DATASET_CACHE_CLOSE_DELAY` (default 5.0 seconds) from the
    environment to configure the cache size and deferred-close delay.
    Intended to be called once, at process startup, before any
    `DatasetsContext` is constructed (see serve_wrapper.py).
    """
    from xcube.webapi.datasets.context import DatasetsContext

    maxsize = int(os.environ.get("XCUBE_DATASET_CACHE_MAXSIZE", str(_DEFAULT_MAXSIZE)))
    close_delay_seconds = float(
        os.environ.get(
            "XCUBE_DATASET_CACHE_CLOSE_DELAY", str(_DEFAULT_CLOSE_DELAY_SECONDS)
        )
    )
    original_init = DatasetsContext.__init__

    def patched_init(self, *args, **kwargs):
        """Replacement `DatasetsContext.__init__`: calls through to the
        original, then swaps in the bounded cache.

        Args:
            *args: Positional arguments forwarded to the original
                `DatasetsContext.__init__`.
            **kwargs: Keyword arguments forwarded to the original
                `DatasetsContext.__init__`.
        """
        original_init(self, *args, **kwargs)
        # Original __init__ already set self._dataset_cache = dict() and
        # nothing has been inserted into it yet at this point -- safe to
        # swap for the bounded version outright.
        self._dataset_cache = _BoundedDatasetCache(maxsize, close_delay_seconds)

    DatasetsContext.__init__ = patched_init
    LOG.warning(
        "xcube dataset cache patched: bounded to %d entries (LRU eviction, "
        "close deferred %.1fs)",
        maxsize,
        close_delay_seconds,
    )
