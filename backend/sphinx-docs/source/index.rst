Global Mosaic documentation
===========================

API reference for the Global Mosaic geostationary satellite mosaic
pipeline. This is generated from the Google-style docstrings already
present in the ``global_mosaic`` package (``backend/src/global_mosaic/``)
and the standalone CLI driver scripts under ``backend/scripts/``.

The package is organized into four subpackages:

* **acquisition** -- per-satellite-family downloaders (GOES, Himawari,
  Meteosat, INSAT) that fetch raw imagery from public data sources.
* **satellites** -- satellite metadata, coverage/slot resolution, and SRF
  (spectral response function) radiometric correction.
* **processing** -- reprojection, satellite selection, blending, and
  mosaic I/O.
* **readers** -- custom satpy file-handler readers for satellite formats
  not natively supported by satpy.

The ``scripts/`` collection holds the standalone, non-package CLI drivers
that tie the pipeline together end to end (phase 0/2 runs, SRF correction
computation, gallery generation, scheduling, etc).

Some third-party dependencies of this package (satpy, pyresample, xarray,
dask, cartopy, netCDF4, h5py, and others -- see ``requirements-docs.txt``
and ``conf.py``'s ``autodoc_mock_imports``) are mocked out for this build
rather than installed, since they are heavy and not needed just to render
docstrings.

.. toctree::
   :maxdepth: 2
   :caption: Package reference:

   api/global_mosaic

.. toctree::
   :maxdepth: 2
   :caption: CLI scripts:

   api/scripts

Indices and tables
===================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
