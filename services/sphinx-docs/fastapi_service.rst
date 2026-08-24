geosat-mosaic-api (FastAPI service)
=====================================

A standalone FastAPI application (title ``GeoSat-MOSAIC metadata API``,
run as the ``geosat-mosaic-api.service`` systemd unit) that serves
per-satellite metadata, point-probe, and timeseries queries directly
against the raw mosaic NetCDF files, plus the ``/minmax`` colorbar
endpoint -- the handful of things THREDDS/xcube cannot serve on their
own. Its real dependencies (``fastapi``, ``pydantic``, ``netCDF4``,
``numpy``) are installed into this docs environment for real (see
``requirements-docs.txt``), since they're lightweight enough to import
directly rather than mock.

main
----

.. automodule:: main
   :members:
   :undoc-members:
   :show-inheritance:

prewarm_minmax
----------------

.. automodule:: prewarm_minmax
   :members:
   :undoc-members:
   :show-inheritance:
