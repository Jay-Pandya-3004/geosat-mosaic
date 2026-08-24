"""Monkeypatches xcube's DatasetsHandler.get (the /datasets route) so its
expensive dataset-listing work runs off the tornado event loop thread.

Root cause (found 2026-08-20): xcube.webapi.datasets.routes.DatasetsHandler.get is a plain
synchronous method that calls get_datasets(), which opens (or re-opens,
after the 40-slot dataset_cache_patch.py LRU eviction) every dataset in
the catalog just to build the listing. xcube's own tornado wrapper
(xcube/server/webservers/tornado.py's TornadoRequestHandler._call_method)
already special-cases coroutine-function handlers by awaiting them
(letting other requests interleave); a plain sync method gets called
directly on the event loop instead, blocking it for however long that
call takes. Diagnosed via py-spy: a /datasets call spent the
full 46s stuck inside dask/xarray rechunk code on MainThread, during
which even a bare "/" request was unresponsive too -- tile-serving
traffic is not exempt.

Other xcube routes (e.g. tile serving) already dispatch their own
blocking work via run_in_executor onto a thread pool (see
dataset_cache_patch.py's docstring: "xcube dispatches requests via a
thread pool"), so this patch brings /datasets in line with the
pattern the rest of the codebase already uses, not a new concurrency
model. That also means concurrent access to
DatasetsContext._dataset_cache from this handler's executor thread is
already a case the bounded-cache patch's deferred-close mechanism was
built to tolerate (see dataset_cache_patch.py's 2026-08-17 fix) -- no
new race class, just one more concurrent caller of an already-concurrent
path.

Uses its own small dedicated executor (not the default loop executor)
so a burst of /datasets calls can't starve whatever else might rely on
the default one, and so its size is an explicit, visible knob
(XCUBE_DATASETS_ROUTE_WORKERS, default 2) rather than an implicit
shared resource.
"""

import asyncio
import functools
import logging
import os
from concurrent.futures import ThreadPoolExecutor

LOG = logging.getLogger("xcube.async_datasets_patch")

_DEFAULT_WORKERS = 2


def apply() -> None:
    """Monkeypatches `xcube.webapi.datasets.routes.DatasetsHandler.get`
    (the `/datasets` route) with an async version that offloads the
    actual `get_datasets()` work to a dedicated `ThreadPoolExecutor`,
    instead of running it synchronously on xcube's tornado event loop
    thread.

    Reads `XCUBE_DATASETS_ROUTE_WORKERS` (default 2) from the environment
    to size the dedicated executor. Intended to be called once, at
    process startup (see serve_wrapper.py).
    """
    from xcube.server.api import ApiError
    from xcube.util.assertions import assert_true
    from xcube.webapi.datasets.controllers import get_datasets
    from xcube.webapi.datasets.routes import DatasetsHandler

    workers = int(
        os.environ.get("XCUBE_DATASETS_ROUTE_WORKERS", str(_DEFAULT_WORKERS))
    )
    executor = ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="xcube-datasets-route"
    )

    async def patched_get(self):
        """Replacement `DatasetsHandler.get`: handles a `GET /datasets`
        request by building the dataset listing on a dedicated executor
        thread instead of the tornado event loop thread.

        Query params (read via `self.request`, matching the original
        handler's behavior):
            details: Whether to include per-dataset detail in the
                listing (passed through to `get_datasets`).
            point: Optional `"lon,lat"` pair; when present, only datasets
                covering that point are included. Parsed into a
                `(float, float)` tuple here; raises `ApiError.BadRequest`
                if it isn't a valid pair of floats.

        Returns:
            None. Writes the JSON response directly via
            `self.response.finish(response)`, matching xcube's own
            handler convention.

        Raises:
            ApiError.BadRequest: If the `point` query argument is present
                but cannot be parsed as two comma-separated floats.
        """
        granted_scopes = self.ctx.auth_ctx.get_granted_scopes(self.request.headers)
        details = self.request.get_query_arg("details", default=False)
        point = self.request.get_query_arg("point", default=None)
        if isinstance(point, str):
            try:
                point = tuple(map(float, point.split(",")))
                assert_true(len(point) == 2, "must be pair of two floats")
            except (ValueError, TypeError) as e:
                raise ApiError.BadRequest(f"illegal point: {e}")
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            executor,
            functools.partial(
                get_datasets,
                self.ctx,
                details=details,
                point=point,
                base_url=self.request.reverse_base_url,
                granted_scopes=granted_scopes,
            ),
        )
        self.response.finish(response)

    DatasetsHandler.get = patched_get
    LOG.warning(
        "xcube /datasets route patched: now runs off the event loop thread "
        "(executor workers=%d)",
        workers,
    )
