GeoSat-MOSAIC Services
=======================

This documents the Python source under ``services/`` that
serves GeoSat-MOSAIC's tiles and metadata. It is **two independent
services** running side by side, not one application:

``xcube/``
    A set of monkeypatches and operational scripts layered on top of the
    third-party `xcube <https://xcube.readthedocs.io/>`_ tile-server
    library, which itself runs as the ``xcube.service`` systemd unit
    (Tornado-based web server, port bound by xcube's own config). These
    modules patch xcube's dataset cache and ``/datasets`` route at
    process startup, provide an xarray coordinate-augmentation hook xcube
    calls per-dataset, and pre-warm xcube's tile output through the
    nginx cache in front of it. None of them are imported directly by an
    end user; ``serve_wrapper.py`` is the actual process entry point
    (invoked by ``xcube.service`` in place of xcube's own ``xcube serve``
    CLI), and the rest are either applied by it at startup or run
    independently as systemd-timer-scheduled scripts.

``fastapi/``
    ``geosat-mosaic-api``, a small standalone FastAPI service (its own
    ``geosat-mosaic-api.service`` systemd unit, independent Python
    process, own port) that serves the handful of features THREDDS/xcube
    cannot: per-satellite metadata, point-probe, and timeseries queries
    against the raw mosaic NetCDF files, plus the ``/minmax`` colorbar
    endpoint. ``prewarm_minmax.py`` is a companion script (its own
    systemd timer) that keeps this service's in-memory caches warm.

Both services read the same rolling-retention-window mosaic files under
``/srv/mosaic-data/tiera`` but neither imports the other or
shares a process.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   xcube
   fastapi_service
